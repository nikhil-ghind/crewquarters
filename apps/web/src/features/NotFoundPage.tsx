import { Compass } from 'lucide-react';
import { ButtonLink } from '../components/Button';
import { EmptyState } from '../components/Feedback';
import { Page, PageHeader } from '../components/Layout';

export default function NotFoundPage() {
  return (
    <Page>
      <PageHeader title="Page not found" purpose="This address does not match any page in Crewquarters." />
      <EmptyState icon={Compass} title="Nothing here" action={<ButtonLink to="/" variant="secondary">Go to Overview</ButtonLink>}>
        Check the address, or use the navigation.
      </EmptyState>
    </Page>
  );
}
